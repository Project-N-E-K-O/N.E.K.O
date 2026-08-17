from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
from types import MethodType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from plugin.plugins.study_companion import StudyCompanionPlugin
from plugin.plugins.study_companion.constants import LLM_OPERATION_CONCEPT_EXPLAIN
from plugin.plugins.study_companion.entry_common import (
    _validate_optional_vision_image_payload,
)
from plugin.plugins.study_companion.entry_tutor_explain_entries import (
    IMAGE_ONLY_EXPLAIN_PROMPT_EN,
    IMAGE_ONLY_EXPLAIN_PROMPT_ZH_CN,
)
from plugin.plugins.study_companion.models import StudyConfig
from plugin.plugins.study_companion.qwen_compatible_transport import QwenCompatibleResult
from plugin.plugins.study_companion.qwen_native_client import (
    QwenNativeClient,
    QwenNativeError,
    QwenNativeResult,
)
from plugin.plugins.study_companion.state import build_initial_state
from plugin.plugins.study_companion.study_ocr_pipeline import StudyOcrPipeline
from plugin.plugins.study_companion.tutor_llm_agent import TutorLLMAgent, TutorReply
from plugin.sdk.plugin import Err, Ok
from plugin.sdk.shared.constants import EVENT_META_ATTR

pytestmark = pytest.mark.unit


class _Logger:
    def __init__(self) -> None:
        self.debugs: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.warnings: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def warning(self, *_args: object, **_kwargs: object) -> None:
        self.warnings.append((_args, _kwargs))
        return None

    def debug(self, *_args: object, **_kwargs: object) -> None:
        self.debugs.append((_args, _kwargs))
        return None


def _model_result(text: str, *, output_limit_reached: bool = False) -> QwenNativeResult:
    return QwenNativeResult(
        text=text,
        model="qwen-test",
        model_group="vision",
        request_id="test-request",
        input_tokens=1,
        output_tokens=1,
        finish_reason="length" if output_limit_reached else "stop",
        max_output_tokens=3072,
        output_limit_reached=output_limit_reached,
    )


class _FakeOcrBackend:
    def extract_text(self, _image: Any) -> str:
        return "ocr text"


class _Store:
    def list_interactions(self, _limit: int) -> list[dict[str, object]]:
        return []

    def append_interaction(self, **_kwargs: object) -> None:
        pass


class _KnowledgeTracker:
    def get_status_summary(self, *, limit: int = 5) -> dict[str, object]:
        return {"limit": limit}


class _VisionPipeline:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def latest_vision_snapshot(self) -> dict[str, object]:
        return dict(self.payload)


JPEG_IMAGE_BASE64 = base64.b64encode(b"\xff\xd8\xff\xe0fake-jpeg").decode("ascii")
PNG_IMAGE_BASE64 = base64.b64encode(b"\x89PNG\r\n\x1a\nfake-png").decode("ascii")
ZH_TRANSFER_EXPECTED_TEXT = (
    "可以把题目中的条件、数值或问法换成同类型设定，"
    "仍按“题目解析 → 解题过程 → 答案”的顺序梳理。"
)


def test_image_only_explain_prompts_require_solution_process_sections() -> None:
    assert "concise, reproducible solution" in IMAGE_ONLY_EXPLAIN_PROMPT_EN
    assert "Solution Process" in IMAGE_ONLY_EXPLAIN_PROMPT_EN
    assert "Answer" in IMAGE_ONLY_EXPLAIN_PROMPT_EN
    assert "verified key derivations" in IMAGE_ONLY_EXPLAIN_PROMPT_EN
    assert "Problem Analysis" in IMAGE_ONLY_EXPLAIN_PROMPT_EN
    assert "Transfer Practice" in IMAGE_ONLY_EXPLAIN_PROMPT_EN
    assert "exactly one short variant" in IMAGE_ONLY_EXPLAIN_PROMPT_EN
    assert "do not guess geometry, labels" in IMAGE_ONLY_EXPLAIN_PROMPT_EN
    assert "do not assume it is single-choice" in IMAGE_ONLY_EXPLAIN_PROMPT_EN
    assert "verify each item independently" in IMAGE_ONLY_EXPLAIN_PROMPT_EN
    assert "output all correct options" in IMAGE_ONLY_EXPLAIN_PROMPT_EN
    assert "精简、可复算的解答" in IMAGE_ONLY_EXPLAIN_PROMPT_ZH_CN
    assert "题目解析" in IMAGE_ONLY_EXPLAIN_PROMPT_ZH_CN
    assert "解题过程" in IMAGE_ONLY_EXPLAIN_PROMPT_ZH_CN
    assert "答案" in IMAGE_ONLY_EXPLAIN_PROMPT_ZH_CN
    assert "举一反三" in IMAGE_ONLY_EXPLAIN_PROMPT_ZH_CN
    assert "恰好给出一道简短变式" in IMAGE_ONLY_EXPLAIN_PROMPT_ZH_CN
    assert "禁止猜测几何关系" in IMAGE_ONLY_EXPLAIN_PROMPT_ZH_CN
    assert "不要默认是单选题" in IMAGE_ONLY_EXPLAIN_PROMPT_ZH_CN
    assert "输出全部正确选项" in IMAGE_ONLY_EXPLAIN_PROMPT_ZH_CN


def test_agent_does_not_guess_vision_support_from_model_name() -> None:
    assert not hasattr(TutorLLMAgent, "_model_supports_vision")


def test_study_explain_text_schema_accepts_vision_image() -> None:
    meta = getattr(StudyCompanionPlugin.study_explain_text, EVENT_META_ATTR)
    properties = meta.input_schema["properties"]

    assert properties["vision_image_base64"] == {
        "type": "string",
        "default": "",
    }


@pytest.mark.parametrize(
    "entry",
    [
        StudyCompanionPlugin.study_generate_question,
        StudyCompanionPlugin.study_evaluate_answer,
    ],
)
def test_structured_study_entries_schema_accepts_vision_image(entry: object) -> None:
    meta = getattr(entry, EVENT_META_ATTR)
    properties = meta.input_schema["properties"]

    assert properties["vision_image_base64"] == {
        "type": "string",
        "default": "",
    }


def test_validate_optional_vision_image_payload_shared_helper() -> None:
    owner = SimpleNamespace(_cfg=StudyConfig(llm_vision_enabled=True), logger=_Logger())

    result = _validate_optional_vision_image_payload(
        owner,
        JPEG_IMAGE_BASE64,
        operation="test_entry",
    )

    assert result == f"data:image/jpeg;base64,{JPEG_IMAGE_BASE64}"

    disabled_owner = SimpleNamespace(
        _cfg=StudyConfig(llm_vision_enabled=False),
        logger=_Logger(),
    )
    disabled = _validate_optional_vision_image_payload(
        disabled_owner,
        JPEG_IMAGE_BASE64,
        operation="test_entry",
    )
    assert isinstance(disabled, Err)
    assert "llm_vision_enabled" in str(disabled.error)

    invalid = _validate_optional_vision_image_payload(
        owner,
        "data:image/webp;base64,abc123",
        operation="test_entry",
    )
    assert isinstance(invalid, Err)
    assert "JPEG/PNG" in str(invalid.error)
    assert owner.logger.warnings


def test_attach_vision_image_adds_to_last_user_msg() -> None:
    messages = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "look here"},
    ]

    result = TutorLLMAgent._attach_vision_image(messages, "abc", detail="high")

    assert result[1]["content"] == "first"
    content = result[3]["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "look here"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == "data:image/jpeg;base64,abc"
    assert content[1]["image_url"]["detail"] == "high"


def test_attach_vision_image_empty_skips() -> None:
    messages = [{"role": "user", "content": "plain"}]

    assert TutorLLMAgent._attach_vision_image(messages, "") is messages


def test_attach_vision_image_only_allows_jpeg_and_png_data_urls() -> None:
    messages = [{"role": "user", "content": "plain"}]

    png = TutorLLMAgent._attach_vision_image(messages, "data:image/png;base64,abc")
    svg = TutorLLMAgent._attach_vision_image(
        messages, "data:image/svg+xml;base64,abc"
    )

    assert png[0]["content"][1]["image_url"]["url"] == "data:image/png;base64,abc"
    assert svg is messages


def test_agent_cannot_strip_image_content_before_provider_call() -> None:
    assert not hasattr(TutorLLMAgent, "_strip_image_content")


@pytest.mark.asyncio
async def test_concept_explain_attaches_vision_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="en"))
    seen: list[dict[str, Any]] = []

    async def _fake_call_model_result(messages: list[dict[str, Any]], **_kwargs: Any):
        seen.extend(messages)
        return _model_result("vision reply")

    monkeypatch.setattr(agent, "_call_model_result", _fake_call_model_result)

    reply = await agent.concept_explain(
        "solve this",
        context={"vision_image_base64": "image-payload"},
    )

    assert reply.reply == "vision reply"
    content = seen[-1]["content"]
    assert isinstance(content, list)
    assert content[1]["image_url"]["url"] == "data:image/jpeg;base64,image-payload"


@pytest.mark.asyncio
async def test_concept_explain_appends_missing_zh_transfer_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="zh-CN"))

    async def _fake_call_model_result(
        _messages: list[dict[str, Any]], **_kwargs: Any
    ):
        return _model_result("题目解析\n分析题意。\n\n解题过程\n列式计算。\n\n答案\nA")

    monkeypatch.setattr(agent, "_call_model_result", _fake_call_model_result)

    reply = await agent.concept_explain(
        IMAGE_ONLY_EXPLAIN_PROMPT_ZH_CN,
        context={
            "vision_image_base64": JPEG_IMAGE_BASE64,
            "study_response_mode": "problem_solving",
        },
    )

    expected_tail = "举一反三\n" + ZH_TRANSFER_EXPECTED_TEXT
    assert reply.reply.rstrip().endswith(expected_tail)
    assert reply.reply.count("举一反三") == 1


@pytest.mark.asyncio
async def test_concept_explain_appends_transfer_to_numbered_zh_solution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="zh-CN"))

    async def _fake_call_model_result(
        _messages: list[dict[str, Any]], **_kwargs: Any
    ):
        return _model_result("1. 分析条件。\n\n2. 计算总和。\n\n答案\nA")

    monkeypatch.setattr(agent, "_call_model_result", _fake_call_model_result)

    reply = await agent.concept_explain(
        IMAGE_ONLY_EXPLAIN_PROMPT_ZH_CN,
        context={
            "vision_image_base64": JPEG_IMAGE_BASE64,
            "study_response_mode": "problem_solving",
        },
    )

    assert reply.reply.rstrip().endswith("举一反三\n" + ZH_TRANSFER_EXPECTED_TEXT)


@pytest.mark.asyncio
async def test_concept_explain_appends_transfer_when_reply_is_zh_but_locale_is_en(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="en"))

    async def _fake_call_model_result(
        _messages: list[dict[str, Any]], **_kwargs: Any
    ):
        return _model_result("4. 计算期望并验证\n\n对分数进行约分。\n\n答案\nA")

    monkeypatch.setattr(agent, "_call_model_result", _fake_call_model_result)

    reply = await agent.concept_explain(
        IMAGE_ONLY_EXPLAIN_PROMPT_ZH_CN,
        context={
            "vision_image_base64": JPEG_IMAGE_BASE64,
            "study_response_mode": "problem_solving",
        },
    )

    assert reply.reply.rstrip().endswith("举一反三\n" + ZH_TRANSFER_EXPECTED_TEXT)


@pytest.mark.asyncio
async def test_concept_explain_does_not_append_transfer_without_answer_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="zh-CN"))

    async def _fake_call_model_result(
        _messages: list[dict[str, Any]], **_kwargs: Any
    ):
        return _model_result(
            "验证 C：若 AB ⊥ CD，则 CD ⊥ 平面 ABD。\n\n"
            "结论：C 错误。\n\n"
            "验证 D：若 AB ⊥ 平面 ACD，则 AC ⊥ AD。\n\n"
            "结论：D 错误。\n\n"
            "结论：B 正确。"
        )

    monkeypatch.setattr(agent, "_call_model_result", _fake_call_model_result)

    reply = await agent.concept_explain(
        IMAGE_ONLY_EXPLAIN_PROMPT_ZH_CN,
        context={"vision_image_base64": JPEG_IMAGE_BASE64},
    )

    assert "举一反三" not in reply.reply
    assert reply.reply.rstrip().endswith("结论：B 正确。")


@pytest.mark.asyncio
async def test_concept_explain_vision_failure_uses_image_specific_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="zh-CN"))

    async def _broken_call_model_result(
        _messages: list[dict[str, Any]], **_kwargs: Any
    ):
        raise RuntimeError("llm unavailable")

    monkeypatch.setattr(agent, "_call_model_result", _broken_call_model_result)

    reply = await agent.concept_explain(
        IMAGE_ONLY_EXPLAIN_PROMPT_ZH_CN,
        context={"vision_image_base64": JPEG_IMAGE_BASE64},
    )

    assert reply.degraded is True
    assert reply.diagnostic == "llm_call_failed"
    assert "视觉模型" in reply.reply
    assert "关键文本" not in reply.reply
    assert "检查问题" not in reply.reply
    assert "请先识别图片中的题目" not in reply.reply


@pytest.mark.asyncio
async def test_structured_operation_attaches_vision_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="en"))
    seen: list[dict[str, Any]] = []

    async def _fake_call_model(
        messages: list[dict[str, Any]],
        *,
        operation: str = "question_generate",
        deadline: float | None = None,
    ):
        seen.extend(messages)
        return json.dumps(
            {
                "question": "What is shown?",
                "answer": "A diagram",
                "hint": "Look at the image.",
                "difficulty": 2,
                "topic": "diagram",
            }
        )

    monkeypatch.setattr(agent, "_call_model", _fake_call_model)

    reply = await agent.question_generate(
        "diagram",
        context={"vision_image_base64": "image-payload"},
    )

    assert reply.payload["question"] == "What is shown?"
    content = seen[-1]["content"]
    assert isinstance(content, list)
    assert content[1]["image_url"]["url"] == "data:image/jpeg;base64,image-payload"


@pytest.mark.asyncio
async def test_call_model_rejects_missing_vision_config_without_text_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from utils import config_manager

    config_groups: list[str] = []

    class _ConfigManager:
        def get_model_api_config(self, group: str) -> dict[str, str]:
            config_groups.append(group)
            if group == "vision":
                return {
                    "base_url": "",
                    "model": "",
                    "api_key": "",
                }
            raise AssertionError("image requests must not fall back to the text model")

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _ConfigManager())
    agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="en"))

    from plugin.plugins.study_companion.study_model_gateway import StudyModelError

    with pytest.raises(StudyModelError) as exc_info:
        await agent._call_model(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "one"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
                    ],
                }
            ]
        )

    assert exc_info.value.diagnostic == "model_unavailable"
    assert config_groups == ["vision"]


@pytest.mark.asyncio
async def test_call_model_uses_vision_config_for_image_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.study_companion import study_model_gateway
    from utils import config_manager

    seen_messages: list[dict[str, Any]] = []
    seen_call_kwargs: list[dict[str, Any]] = []
    config_groups: list[str] = []

    class _ConfigManager:
        def get_model_api_config(self, group: str) -> dict[str, str]:
            config_groups.append(group)
            if group == "vision":
                return {
                    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "model": "qwen3.7-plus",
                    "api_key": "vision-key",
                }
            raise AssertionError("vision request unexpectedly used the agent model")

    class _FakeGenericClient:
        async def ainvoke(self, messages: list[dict[str, Any]]) -> SimpleNamespace:
            seen_messages.extend(messages)
            return SimpleNamespace(
                content="vision reply",
                response_metadata={
                    "request_id": "request-1",
                    "finish_reason": "stop",
                    "token_usage": {"prompt_tokens": 12, "completion_tokens": 4},
                },
            )

        async def aclose(self) -> None:
            return None

    async def _fake_factory(**kwargs: Any) -> _FakeGenericClient:
        seen_call_kwargs.append(dict(kwargs))
        return _FakeGenericClient()

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _ConfigManager())
    monkeypatch.setattr(
        study_model_gateway,
        "create_chat_llm_async",
        _fake_factory,
    )
    agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="en"))

    result = await agent._call_model(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "what is shown?"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
                ],
            }
        ],
        operation="knowledge_semantic_route",
    )

    assert result == "vision reply"
    assert config_groups == ["vision"]
    assert seen_call_kwargs[0]["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert seen_call_kwargs[0]["model"] == "qwen3.7-plus"
    assert seen_call_kwargs[0]["api_key"] == "vision-key"
    assert seen_call_kwargs[0]["provider_type"] == "openai_compatible"
    assert seen_call_kwargs[0]["max_completion_tokens"] == 512
    assert seen_call_kwargs[0]["max_retries"] == 0
    content = seen_messages[0]["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "what is shown?"}
    assert content[1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,x"},
    }


@pytest.mark.parametrize(
    "model",
    ["qwen3.7-plus", "qwen3.7-plus-2026-05-26"],
)
@pytest.mark.asyncio
async def test_qwen37_plus_text_uses_agent_config_with_compatible_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    model: str,
) -> None:
    from plugin.plugins.study_companion import qwen_native_client
    from utils import config_manager

    config_groups: list[str] = []
    compatible_calls: list[dict[str, Any]] = []

    class _ConfigManager:
        def get_model_api_config(self, group: str) -> dict[str, str]:
            config_groups.append(group)
            assert group == "agent"
            return {
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "model": model,
                "api_key": "agent-key",
            }

    class _CompatibleTransport:
        async def chat_completions(self, **kwargs: Any) -> QwenCompatibleResult:
            compatible_calls.append(dict(kwargs))
            return QwenCompatibleResult(
                text="《活着》呈现了苦难中的生命韧性。",
                model=model,
                request_id="text-request",
                input_tokens=8,
                output_tokens=5,
            )

    class _UnexpectedGeneration:
        @staticmethod
        async def call(**_kwargs: Any) -> SimpleNamespace:
            raise AssertionError("compatible text must not use native generation")

    class _UnexpectedMultiModalConversation:
        @staticmethod
        async def call(**_kwargs: Any) -> SimpleNamespace:
            raise AssertionError("text-only requests must not use multimodal generation")

    async def _discard_usage(_client: object, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _ConfigManager())
    monkeypatch.setattr(
        qwen_native_client,
        "AioMultiModalConversation",
        _UnexpectedMultiModalConversation,
    )
    monkeypatch.setattr(qwen_native_client, "AioGeneration", _UnexpectedGeneration)
    monkeypatch.setattr(QwenNativeClient, "_record_usage", _discard_usage)
    client = QwenNativeClient(logger=_Logger())
    client._compatible_transport = _CompatibleTransport()  # type: ignore[assignment]

    result = await client.call(
        [{"role": "user", "content": "谈谈你对活着这本书的理解"}],
        operation=LLM_OPERATION_CONCEPT_EXPLAIN,
        deadline=time.monotonic() + 10.0,
    )

    assert result.text == "《活着》呈现了苦难中的生命韧性。"
    assert result.model_group == "agent"
    assert config_groups == ["agent"]
    assert len(compatible_calls) == 1
    assert compatible_calls[0]["model"] == model
    assert compatible_calls[0]["messages"] == [
        {"role": "user", "content": "谈谈你对活着这本书的理解"}
    ]


@pytest.mark.parametrize(
    "model",
    ["qwen-plus", "qwen3.7-plus-future", "qwen-future-experimental"],
)
@pytest.mark.asyncio
async def test_other_qwen_text_models_keep_text_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    model: str,
) -> None:
    from plugin.plugins.study_companion import qwen_native_client
    from utils import config_manager

    compatible_calls: list[dict[str, Any]] = []

    class _ConfigManager:
        def get_model_api_config(self, group: str) -> dict[str, str]:
            assert group == "agent"
            return {
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "model": model,
                "api_key": "agent-key",
            }

    class _CompatibleTransport:
        async def chat_completions(self, **kwargs: Any) -> QwenCompatibleResult:
            compatible_calls.append(dict(kwargs))
            return QwenCompatibleResult(
                text="text reply",
                model=model,
                request_id="text-request",
                input_tokens=1,
                output_tokens=1,
            )

    class _UnexpectedGeneration:
        @staticmethod
        async def call(**_kwargs: Any) -> SimpleNamespace:
            raise AssertionError("compatible text must not use native generation")

    class _UnexpectedMultiModalConversation:
        @staticmethod
        async def call(**_kwargs: Any) -> SimpleNamespace:
            raise AssertionError("unregistered models must keep text-generation")

    async def _discard_usage(_client: object, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _ConfigManager())
    monkeypatch.setattr(qwen_native_client, "AioGeneration", _UnexpectedGeneration)
    monkeypatch.setattr(
        qwen_native_client,
        "AioMultiModalConversation",
        _UnexpectedMultiModalConversation,
    )
    monkeypatch.setattr(QwenNativeClient, "_record_usage", _discard_usage)
    client = QwenNativeClient(logger=_Logger())
    client._compatible_transport = _CompatibleTransport()  # type: ignore[assignment]

    result = await client.call(
        [{"role": "user", "content": "hello"}],
        operation=LLM_OPERATION_CONCEPT_EXPLAIN,
        deadline=time.monotonic() + 10.0,
    )

    assert result.text == "text reply"
    assert result.model_group == "agent"
    assert len(compatible_calls) == 1
    assert compatible_calls[0]["model"] == model
    assert compatible_calls[0]["messages"] == [
        {"role": "user", "content": "hello"}
    ]


@pytest.mark.asyncio
async def test_qwen_compatible_result_reports_length_finish_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.study_companion import qwen_native_client
    from utils import config_manager

    class _ConfigManager:
        def get_model_api_config(self, group: str) -> dict[str, str]:
            assert group == "agent"
            return {
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "model": "qwen3.7-plus",
                "api_key": "agent-key",
            }

    class _CompatibleTransport:
        async def chat_completions(self, **_kwargs: Any) -> QwenCompatibleResult:
            return QwenCompatibleResult(
                text="truncated explanation",
                model="qwen3.7-plus",
                request_id="compatible-length-request",
                input_tokens=17,
                output_tokens=29,
                finish_reason="length",
            )

    async def _discard_usage(_client: object, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _ConfigManager())
    monkeypatch.setattr(
        qwen_native_client,
        "AioMultiModalConversation",
        None,
    )
    monkeypatch.setattr(QwenNativeClient, "_record_usage", _discard_usage)
    logger = _Logger()
    client = QwenNativeClient(logger=logger)
    client._compatible_transport = _CompatibleTransport()  # type: ignore[assignment]

    result = await client.call(
        [{"role": "user", "content": "sensitive question text"}],
        operation=LLM_OPERATION_CONCEPT_EXPLAIN,
        deadline=time.monotonic() + 10.0,
    )

    assert result.finish_reason == "length"
    assert result.input_tokens == 17
    assert result.output_tokens == 29
    assert result.max_output_tokens == 3072
    assert result.output_limit_reached is True
    assert logger.warnings == [
        (
            (
                "study Qwen output limit reached: diagnostic=output_truncated "
                "operation={} model_group={} "
                "finish_reason={} output_tokens={} max_output_tokens={}",
                LLM_OPERATION_CONCEPT_EXPLAIN,
                "agent",
                "length",
                29,
                3072,
            ),
            {},
        )
    ]
    assert "sensitive question text" not in repr(logger.warnings)
    assert "truncated explanation" not in repr(logger.warnings)


@pytest.mark.asyncio
async def test_qwen_solution_structure_repair_has_bounded_observable_output_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.study_companion import qwen_native_client
    from utils import config_manager

    calls: list[dict[str, Any]] = []

    class _ConfigManager:
        def get_model_api_config(self, group: str) -> dict[str, str]:
            assert group == "agent"
            return {
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "model": "qwen-plus",
                "api_key": "agent-key",
            }

    class _CompatibleTransport:
        async def chat_completions(self, **kwargs: Any) -> QwenCompatibleResult:
            calls.append(dict(kwargs))
            return QwenCompatibleResult(
                text="complete json correction",
                model="qwen-plus",
                request_id="text-budget-request",
                input_tokens=11,
                output_tokens=1536,
                finish_reason="stop",
            )

    async def _discard_usage(_client: object, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _ConfigManager())
    monkeypatch.setattr(QwenNativeClient, "_record_usage", _discard_usage)
    logger = _Logger()
    client = QwenNativeClient(logger=logger)
    client._compatible_transport = _CompatibleTransport()  # type: ignore[assignment]

    result = await client.call(
        [{"role": "user", "content": "repair json"}],
        operation="solution_structure_repair",
        deadline=time.monotonic() + 10.0,
    )

    assert result.finish_reason == "stop"
    assert result.input_tokens == 11
    assert result.output_tokens == 1536
    assert result.max_output_tokens == 1536
    assert result.output_limit_reached is False
    assert calls[0]["max_tokens"] == 1536
    assert logger.warnings == []


@pytest.mark.asyncio
async def test_qwen_semantic_route_uses_bounded_text_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.study_companion import qwen_native_client
    from utils import config_manager

    calls: list[dict[str, Any]] = []

    class _ConfigManager:
        def get_model_api_config(self, group: str) -> dict[str, str]:
            assert group == "agent"
            return {
                "model": "qwen-plus",
                "base_url": "https://dashscope.aliyuncs.com/api/v1",
                "api_key": "test-key",
            }

    class _FakeGeneration:
        @staticmethod
        async def call(**kwargs: Any) -> SimpleNamespace:
            calls.append(dict(kwargs))
            return SimpleNamespace(
                status_code=200,
                output=SimpleNamespace(text='{"subject":"unknown"}'),
                usage=SimpleNamespace(input_tokens=2, output_tokens=3),
            )

    async def _discard_usage(_client: object, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _ConfigManager())
    monkeypatch.setattr(qwen_native_client, "AioGeneration", _FakeGeneration)
    monkeypatch.setattr(QwenNativeClient, "_record_usage", _discard_usage)
    client = QwenNativeClient(logger=_Logger())

    result = await client.call(
        [{"role": "user", "content": "classify semantics"}],
        operation="knowledge_semantic_route",
        deadline=time.monotonic() + 10.0,
    )

    assert result.max_output_tokens == 512
    assert calls[0]["max_tokens"] == 512
    assert result.output_limit_reached is False


@pytest.mark.parametrize(
    ("status_code", "code", "message", "expected"),
    [
        (401, "InvalidApiKey", "unauthorized", "authentication_failed"),
        (403, "Forbidden", "forbidden", "authentication_failed"),
        (429, "Throttling", "too many requests", "rate_limited"),
        (503, "ServiceUnavailable", "provider down", "provider_unavailable"),
        (400, "InvalidParameter", "invalid image payload", "invalid_image"),
        (
            400,
            "InvalidParameter",
            "url error; https://help.aliyun.com/zh/model-studio/error-code#error-url",
            "invalid_endpoint",
        ),
        (400, "InvalidParameter", "unsupported request parameter", "invalid_request"),
        (404, "NotFound", "route not found", "invalid_endpoint"),
        (400, "ModelNotFound", "configured model is unavailable", "model_not_supported"),
    ],
)
def test_qwen_native_response_diagnostics(
    status_code: int, code: str, message: str, expected: str
) -> None:
    from plugin.plugins.study_companion import qwen_native_client

    response = SimpleNamespace(status_code=status_code, code=code, message=message)

    assert qwen_native_client._diagnostic_for_response(response) == expected


@pytest.mark.asyncio
async def test_qwen_native_deadline_exhaustion_skips_sdk_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.study_companion import qwen_native_client
    from utils import config_manager

    calls: list[dict[str, Any]] = []

    class _ConfigManager:
        def get_model_api_config(self, group: str) -> dict[str, str]:
            assert group == "agent"
            return {
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "model": "qwen-plus",
                "api_key": "text-key",
            }

    class _FakeGeneration:
        @staticmethod
        async def call(**kwargs: Any) -> SimpleNamespace:
            calls.append(dict(kwargs))
            raise AssertionError("expired requests must not reach DashScope")

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _ConfigManager())
    monkeypatch.setattr(qwen_native_client, "AioGeneration", _FakeGeneration)
    client = QwenNativeClient(logger=_Logger())
    monkeypatch.setattr(client, "_record_usage", AsyncMock())

    with pytest.raises(QwenNativeError) as raised:
        await client.call(
            [{"role": "user", "content": "hello"}],
            operation="concept_explain",
            deadline=time.monotonic() - 1.0,
        )

    assert raised.value.diagnostic == "timeout"
    assert calls == []


@pytest.mark.asyncio
async def test_qwen_native_timeout_cancels_sdk_call_without_background_close_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.study_companion import qwen_native_client
    from utils import config_manager

    cancelled = asyncio.Event()

    class _ConfigManager:
        def get_model_api_config(self, group: str) -> dict[str, str]:
            assert group == "agent"
            return {
                "base_url": "https://dashscope.aliyuncs.com/api/v1",
                "model": "qwen-plus",
                "api_key": "text-key",
            }

    class _FakeGeneration:
        @staticmethod
        async def call(**_kwargs: Any) -> SimpleNamespace:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    async def _discard_usage(_client: object, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _ConfigManager())
    monkeypatch.setattr(qwen_native_client, "AioGeneration", _FakeGeneration)
    monkeypatch.setattr(QwenNativeClient, "_record_usage", _discard_usage)
    client = QwenNativeClient(logger=_Logger())
    current = asyncio.current_task()
    tasks_before = {task for task in asyncio.all_tasks() if task is not current}

    with pytest.raises(QwenNativeError) as raised:
        await client.call(
            [{"role": "user", "content": "hello"}],
            operation="concept_explain",
            deadline=time.monotonic() + 0.02,
        )
    await asyncio.sleep(0)

    tasks_after = {
        task for task in asyncio.all_tasks() if task is not current and not task.done()
    }
    assert cancelled.is_set()
    assert raised.value.diagnostic == "timeout"
    assert tasks_after <= tasks_before


@pytest.mark.asyncio
async def test_qwen_native_network_failure_is_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.study_companion import qwen_native_client
    from utils import config_manager

    class _ConfigManager:
        def get_model_api_config(self, group: str) -> dict[str, str]:
            assert group == "agent"
            return {
                "base_url": "https://dashscope.aliyuncs.com/api/v1",
                "model": "qwen-plus",
                "api_key": "text-key",
            }

    class _FakeGeneration:
        @staticmethod
        async def call(**_kwargs: Any) -> SimpleNamespace:
            raise ConnectionError("network unavailable")

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _ConfigManager())
    monkeypatch.setattr(qwen_native_client, "AioGeneration", _FakeGeneration)
    client = QwenNativeClient(logger=_Logger())
    monkeypatch.setattr(client, "_record_usage", AsyncMock())

    with pytest.raises(QwenNativeError) as raised:
        await client.call(
            [{"role": "user", "content": "hello"}],
            operation="concept_explain",
            deadline=time.monotonic() + 1.0,
        )

    assert raised.value.diagnostic == "provider_unavailable"


@pytest.mark.asyncio
async def test_json_correction_reuses_deadline_and_strips_image_payload() -> None:
    agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="en"))
    calls: list[tuple[str, float, list[dict[str, Any]]]] = []
    deadline = time.monotonic() + 30.0

    async def _fake_call_model(
        messages: list[dict[str, Any]], *, operation: str, deadline: float
    ) -> str:
        calls.append((operation, deadline, messages))
        if len(calls) == 1:
            return "not valid json"
        return '{"question":"What is shown?","answer":"A diagram"}'

    raw = await agent._json_corrector.invoke_with_correction(
        operation="question_generate",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "make a question"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64,sensitive-image"
                        },
                    },
                ],
            }
        ],
        call_model=_fake_call_model,
        deadline=deadline,
    )

    assert json.loads(raw)["question"] == "What is shown?"
    assert [call[0] for call in calls] == ["question_generate", "json_correction"]
    assert [call[1] for call in calls] == [deadline, deadline]
    correction_messages = calls[1][2]
    assert all(isinstance(message["content"], str) for message in correction_messages)
    assert "sensitive-image" not in repr(correction_messages)
    assert "image_url" not in repr(correction_messages)


@pytest.mark.asyncio
async def test_qwen_json_correction_uses_text_client_and_1536_token_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.study_companion import qwen_native_client
    from utils import config_manager

    calls: list[dict[str, Any]] = []

    class _ConfigManager:
        def get_model_api_config(self, group: str) -> dict[str, str]:
            assert group == "agent"
            return {
                "base_url": "https://dashscope.aliyuncs.com/api/v1",
                "model": "qwen-plus",
                "api_key": "text-key",
            }

    class _FakeGeneration:
        @staticmethod
        async def call(**kwargs: Any) -> SimpleNamespace:
            calls.append(dict(kwargs))
            return SimpleNamespace(
                status_code=200,
                request_id="correction-request",
                output=SimpleNamespace(text='{"question":"fixed"}'),
                usage=SimpleNamespace(input_tokens=2, output_tokens=3),
            )

    async def _discard_usage(_client: object, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _ConfigManager())
    monkeypatch.setattr(qwen_native_client, "AioGeneration", _FakeGeneration)
    monkeypatch.setattr(QwenNativeClient, "_record_usage", _discard_usage)
    client = QwenNativeClient(logger=_Logger())

    result = await client.call(
        [{"role": "user", "content": "repair the json"}],
        operation="json_correction",
        deadline=time.monotonic() + 10.0,
    )

    assert result.text == '{"question":"fixed"}'
    assert calls[0]["max_tokens"] == 1536
    assert calls[0]["enable_thinking"] is False
    assert calls[0]["request_timeout"] > 0


def test_study_state_to_dict_excludes_transient_vision_image() -> None:
    state = build_initial_state()
    state.last_vision_image_base64 = "sensitive-image"

    payload = state.to_dict()

    assert "last_vision_image_base64" not in payload


def test_remember_vision_snapshot_encodes_jpeg_and_resizes() -> None:
    pipeline = StudyOcrPipeline(
        logger=_Logger(),
        config=StudyConfig(llm_vision_enabled=True, llm_vision_max_image_px=96),
        ocr_backend=_FakeOcrBackend(),
    )
    image = Image.new("RGB", (320, 160), color="white")

    snapshot = pipeline.snapshot_from_image(image)
    vision = pipeline.latest_vision_snapshot()

    assert snapshot.status == "ok"
    assert str(vision["vision_image_base64"]).startswith("data:image/jpeg;base64,")
    assert vision["width"] == 96
    assert vision["height"] == 48
    raw = base64.b64decode(str(vision["vision_image_base64"]).split(",", 1)[1])
    assert raw.startswith(b"\xff\xd8")


def test_latest_vision_snapshot_expires_and_respects_disabled_config() -> None:
    pipeline = StudyOcrPipeline(
        logger=_Logger(),
        config=StudyConfig(llm_vision_enabled=True),
        ocr_backend=_FakeOcrBackend(),
    )
    image = Image.new("RGB", (16, 16), color="white")

    pipeline._remember_vision_snapshot(image)
    assert pipeline.latest_vision_snapshot()

    pipeline._latest_vision_snapshot["expires_at_monotonic"] = 0.0
    assert pipeline.latest_vision_snapshot() == {}

    pipeline._remember_vision_snapshot(image)
    pipeline.update_config(StudyConfig(llm_vision_enabled=False))
    assert pipeline.latest_vision_snapshot() == {}


def test_remember_vision_snapshot_logs_empty_return_paths() -> None:
    class _InvalidImage:
        size = (0, 10)

        def save(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("invalid image must not be encoded")

    class _EmptyImage:
        size = (16, 16)

        def save(self, *_args: object, **_kwargs: object) -> None:
            return None

    logger = _Logger()
    pipeline = StudyOcrPipeline(
        logger=logger,
        config=StudyConfig(llm_vision_enabled=True),
        ocr_backend=_FakeOcrBackend(),
    )

    pipeline._remember_vision_snapshot(_InvalidImage())
    pipeline._remember_vision_snapshot(_EmptyImage())

    messages = [str(item[0][0]) for item in logger.debugs]
    assert any("invalid image dimensions" in message for message in messages)
    assert any("empty encoded buffer" in message for message in messages)


def test_remember_vision_snapshot_clears_stale_snapshot_on_abort() -> None:
    class _InvalidImage:
        size = (0, 10)

        def save(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("invalid image must not be encoded")

    pipeline = StudyOcrPipeline(
        logger=_Logger(),
        config=StudyConfig(llm_vision_enabled=True),
        ocr_backend=_FakeOcrBackend(),
    )
    image = Image.new("RGB", (16, 16), color="white")

    pipeline._remember_vision_snapshot(image)
    assert pipeline.latest_vision_snapshot()

    pipeline._remember_vision_snapshot(_InvalidImage())

    assert pipeline.latest_vision_snapshot() == {}


def test_capture_snapshot_clears_stale_vision_snapshot_on_early_failure() -> None:
    class _FailingCaptureBackend:
        def capture_frame(self, *_args: object, **_kwargs: object) -> object:
            raise RuntimeError("capture boom")

    pipeline = StudyOcrPipeline(
        logger=_Logger(),
        config=StudyConfig(llm_vision_enabled=True),
        ocr_backend=_FakeOcrBackend(),
        capture_backend=_FailingCaptureBackend(),
    )
    image = Image.new("RGB", (16, 16), color="white")

    pipeline._remember_vision_snapshot(image)
    assert pipeline.latest_vision_snapshot()

    failed = pipeline.capture_snapshot(target=object())

    assert failed.status == "capture_failed"
    assert pipeline.latest_vision_snapshot() == {}


def test_capture_snapshot_clears_stale_vision_snapshot_when_ocr_disabled() -> None:
    pipeline = StudyOcrPipeline(
        logger=_Logger(),
        config=StudyConfig(llm_vision_enabled=True),
        ocr_backend=_FakeOcrBackend(),
    )
    image = Image.new("RGB", (16, 16), color="white")

    pipeline._remember_vision_snapshot(image)
    assert pipeline.latest_vision_snapshot()

    pipeline._config = StudyConfig(ocr_enabled=False, llm_vision_enabled=True)
    disabled = pipeline.capture_snapshot()

    assert disabled.status == "disabled"
    assert pipeline.latest_vision_snapshot() == {}


def test_remember_vision_snapshot_warns_on_memory_error() -> None:
    class _MemoryErrorImage:
        size = (16, 16)

        def save(self, *_args: object, **_kwargs: object) -> None:
            raise MemoryError("boom")

    logger = _Logger()
    pipeline = StudyOcrPipeline(
        logger=logger,
        config=StudyConfig(llm_vision_enabled=True),
        ocr_backend=_FakeOcrBackend(),
    )

    pipeline._remember_vision_snapshot(_MemoryErrorImage())

    assert logger.warnings
    assert "memory error" in str(logger.warnings[0][0][0])


@pytest.mark.asyncio
async def test_build_learning_context_keeps_user_image_until_submit_success() -> None:
    plugin = StudyCompanionPlugin.__new__(StudyCompanionPlugin)
    plugin._cfg = StudyConfig(llm_vision_enabled=True)
    plugin._state = build_initial_state()
    plugin._state.last_vision_image_base64 = "user-image"
    plugin._store = _Store()
    plugin._knowledge_tracker = _KnowledgeTracker()
    plugin._ocr_pipeline = _VisionPipeline({"vision_image_base64": "ocr-image"})
    plugin._lock = threading.RLock()

    context = await plugin._build_learning_context(
        LLM_OPERATION_CONCEPT_EXPLAIN,
        input_text="explain",
    )
    second_context = await plugin._build_learning_context(
        LLM_OPERATION_CONCEPT_EXPLAIN,
        input_text="explain",
    )

    assert context["vision_enabled"] is True
    assert context["vision_image_base64"] == "user-image"
    assert plugin._state.last_vision_image_base64 == "user-image"
    assert second_context["vision_image_base64"] == "user-image"


@pytest.mark.asyncio
async def test_study_submit_image_stores_base64_and_delegates() -> None:
    plugin = StudyCompanionPlugin.__new__(StudyCompanionPlugin)
    plugin._cfg = StudyConfig(llm_vision_enabled=True)
    plugin._state = build_initial_state()
    plugin._lock = threading.RLock()
    plugin._persist_state_calls = 0
    calls: list[tuple[str, str]] = []

    async def _study_explain_text(self: StudyCompanionPlugin, text: str = "", **kwargs: Any):
        calls.append((text, str(kwargs.get("vision_image_base64") or "")))
        assert self._state.last_vision_image_base64 == ""
        return Ok({"reply": "done"})

    async def _persist_state(self: StudyCompanionPlugin) -> None:
        self._persist_state_calls += 1

    plugin.study_explain_text = MethodType(_study_explain_text, plugin)
    plugin._persist_state = MethodType(_persist_state, plugin)

    result = await plugin.study_submit_image(JPEG_IMAGE_BASE64, text="solve this")

    assert isinstance(result, Ok)
    assert calls == [("solve this", f"data:image/jpeg;base64,{JPEG_IMAGE_BASE64}")]
    assert plugin._state.last_vision_image_base64 == ""
    assert plugin._persist_state_calls == 0


@pytest.mark.asyncio
async def test_study_submit_image_without_caption_preserves_ocr_fallback() -> None:
    plugin = StudyCompanionPlugin.__new__(StudyCompanionPlugin)
    plugin._cfg = StudyConfig(llm_vision_enabled=True)
    plugin._state = build_initial_state()
    plugin._state.last_ocr_text = "previous OCR context"
    plugin._lock = threading.RLock()
    calls: list[str] = []

    async def _study_explain_text(self: StudyCompanionPlugin, text: str = "", **_: Any):
        calls.append(text)
        return Ok({"reply": "done"})

    plugin.study_explain_text = MethodType(_study_explain_text, plugin)

    result = await plugin.study_submit_image(JPEG_IMAGE_BASE64)

    assert isinstance(result, Ok)
    assert calls == [IMAGE_ONLY_EXPLAIN_PROMPT_ZH_CN]
    assert plugin._state.last_ocr_text == "previous OCR context"


@pytest.mark.asyncio
async def test_study_submit_image_without_caption_uses_english_prompt() -> None:
    plugin = StudyCompanionPlugin.__new__(StudyCompanionPlugin)
    plugin._cfg = StudyConfig(llm_vision_enabled=True, language="en")
    plugin._state = build_initial_state()
    plugin._lock = threading.RLock()
    calls: list[str] = []

    async def _study_explain_text(self: StudyCompanionPlugin, text: str = "", **_: Any):
        calls.append(text)
        return Ok({"reply": "explained"})

    plugin.study_explain_text = MethodType(_study_explain_text, plugin)

    result = await plugin.study_submit_image(JPEG_IMAGE_BASE64)

    assert isinstance(result, Ok)
    assert calls == [IMAGE_ONLY_EXPLAIN_PROMPT_EN]


@pytest.mark.asyncio
async def test_study_submit_image_uses_call_local_image_for_overlap() -> None:
    plugin = StudyCompanionPlugin.__new__(StudyCompanionPlugin)
    plugin._cfg = StudyConfig(llm_vision_enabled=True)
    plugin._state = build_initial_state()
    plugin._lock = threading.RLock()
    seen: list[tuple[str, str]] = []

    async def _study_explain_text(self: StudyCompanionPlugin, text: str = "", **kwargs: Any):
        await asyncio.sleep(0)
        seen.append((text, str(kwargs.get("vision_image_base64") or "")))
        assert self._state.last_vision_image_base64 == ""
        return Ok({"reply": text})

    plugin.study_explain_text = MethodType(_study_explain_text, plugin)

    results = await asyncio.gather(
        plugin.study_submit_image(JPEG_IMAGE_BASE64, text="first"),
        plugin.study_submit_image(f"data:image/png;base64,{PNG_IMAGE_BASE64}", text="second"),
    )

    assert all(isinstance(result, Ok) for result in results)
    assert sorted(seen) == [
        ("first", f"data:image/jpeg;base64,{JPEG_IMAGE_BASE64}"),
        ("second", f"data:image/png;base64,{PNG_IMAGE_BASE64}"),
    ]
    assert plugin._state.last_vision_image_base64 == ""


@pytest.mark.asyncio
async def test_study_submit_image_rejects_oversized_base64() -> None:
    plugin = StudyCompanionPlugin.__new__(StudyCompanionPlugin)
    plugin._cfg = StudyConfig(llm_vision_enabled=True)
    plugin._state = build_initial_state()
    plugin._lock = threading.RLock()

    oversized = base64.b64encode(b"\xff\xd8\xff" + b"x" * (10 * 1024 * 1024 + 1)).decode(
        "ascii"
    )

    result = await plugin.study_submit_image(oversized)

    assert isinstance(result, Err)
    assert "too large" in str(result.error)


@pytest.mark.asyncio
async def test_study_submit_image_rejects_invalid_mime_and_base64() -> None:
    plugin = StudyCompanionPlugin.__new__(StudyCompanionPlugin)
    plugin._cfg = StudyConfig(llm_vision_enabled=True)
    plugin._state = build_initial_state()
    plugin._lock = threading.RLock()

    bad_mime = await plugin.study_submit_image("data:image/webp;base64,abc")
    bad_base64 = await plugin.study_submit_image("data:image/png;base64,not base64")

    assert isinstance(bad_mime, Err)
    assert "JPEG/PNG" in str(bad_mime.error)
    assert isinstance(bad_base64, Err)
    assert "valid base64" in str(bad_base64.error)


@pytest.mark.asyncio
async def test_study_submit_image_keeps_base64_when_delegate_fails() -> None:
    plugin = StudyCompanionPlugin.__new__(StudyCompanionPlugin)
    plugin._cfg = StudyConfig(llm_vision_enabled=True)
    plugin._state = build_initial_state()
    plugin._lock = threading.RLock()

    async def _study_explain_text(self: StudyCompanionPlugin, text: str = "", **_: Any):
        assert text
        return Err(RuntimeError("failed"))

    plugin.study_explain_text = MethodType(_study_explain_text, plugin)

    result = await plugin.study_submit_image(
        f"data:image/png;base64,{PNG_IMAGE_BASE64}",
        text="solve this",
    )

    assert isinstance(result, Err)
    assert plugin._state.last_vision_image_base64 == ""


@pytest.mark.asyncio
async def test_study_submit_image_requires_enabled_config() -> None:
    plugin = StudyCompanionPlugin.__new__(StudyCompanionPlugin)
    plugin._cfg = StudyConfig(llm_vision_enabled=False)
    plugin._state = build_initial_state()
    plugin._lock = threading.RLock()

    result = await plugin.study_submit_image(JPEG_IMAGE_BASE64)

    assert isinstance(result, Err)
    assert "llm_vision_enabled" in str(result.error)


class _FakeVisionTutorAgent:
    def __init__(self) -> None:
        self.explanations: list[tuple[str, dict[str, object], str]] = []
        self.generated_questions: list[tuple[str, dict[str, object], str]] = []
        self.evaluations: list[tuple[str, str, str, dict[str, object], str]] = []

    async def concept_explain(
        self,
        text: str,
        *,
        mode: str = "companion",
        context: dict[str, object] | None = None,
    ) -> TutorReply:
        self.explanations.append((text, dict(context or {}), mode))
        return TutorReply(
            operation="concept_explain",
            input_text=text,
            reply=f"explained: {text}",
            created_at="2026-05-25T00:00:00Z",
        )

    async def question_generate(
        self,
        text: str,
        *,
        mode: str = "companion",
        context: dict[str, object] | None = None,
    ) -> TutorReply:
        self.generated_questions.append((text, dict(context or {}), mode))
        return TutorReply(
            operation="question_generate",
            input_text=text,
            reply="question generated",
            payload={
                "question": "What is shown?",
                "answer": "A diagram",
                "hint": "Use the visual context.",
                "difficulty": 2,
                "topic": "diagram",
            },
            created_at="2026-05-25T00:00:00Z",
        )

    async def answer_evaluate(
        self,
        *,
        question: str = "",
        answer: str = "",
        expected_answer: str = "",
        mode: str = "companion",
        context: dict[str, object] | None = None,
    ) -> TutorReply:
        self.evaluations.append(
            (question, answer, expected_answer, dict(context or {}), mode)
        )
        return TutorReply(
            operation="answer_evaluate",
            input_text=answer,
            reply="answer evaluated",
            payload={
                "verdict": "partial",
                "score": 50,
                "feedback": "Compare it with the image.",
                "next_action": "Review the diagram.",
            },
            created_at="2026-05-25T00:00:00Z",
        )

    async def shutdown(self) -> None:
        pass


class _StructuredVisionKnowledgeTracker:
    def get_status_summary(self, *, limit: int = 5) -> dict[str, object]:
        return {"limit": limit}

    def get_next_question_params(self, _hint: str = "") -> dict[str, object]:
        return {"topic": "diagram"}

    def get_mastery(self, _topic: str) -> float:
        return 0.5


def _make_plugin_for_explain(
    *, vision_enabled: bool, language: str = "zh-CN"
) -> StudyCompanionPlugin:
    plugin = StudyCompanionPlugin.__new__(StudyCompanionPlugin)
    plugin._cfg = StudyConfig(llm_vision_enabled=vision_enabled, language=language)
    plugin._state = build_initial_state()
    plugin._lock = asyncio.Lock()
    plugin._agent = _FakeVisionTutorAgent()
    plugin._store = _Store()
    plugin._knowledge_tracker = _KnowledgeTracker()
    plugin._ocr_pipeline = None
    plugin._persist_state_calls = 0

    async def _persist_state(self: StudyCompanionPlugin) -> None:
        self._persist_state_calls += 1

    plugin._persist_state = MethodType(_persist_state, plugin)
    return plugin


def _make_plugin_for_structured_vision(
    *, vision_enabled: bool, language: str = "zh-CN"
) -> StudyCompanionPlugin:
    plugin = StudyCompanionPlugin.__new__(StudyCompanionPlugin)
    plugin._cfg = StudyConfig(llm_vision_enabled=vision_enabled, language=language)
    plugin._state = build_initial_state()
    plugin._lock = asyncio.Lock()
    plugin._agent = _FakeVisionTutorAgent()
    plugin._knowledge_tracker = _StructuredVisionKnowledgeTracker()
    plugin.logger = _Logger()

    async def _build_learning_context(
        self: StudyCompanionPlugin,
        operation: str,
        *,
        input_text: str = "",
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "operation": operation,
            "input_text": input_text,
            **dict(extra or {}),
        }

    async def _finalize_tutor_call(
        self: StudyCompanionPlugin,
        _operation: str,
        reply: TutorReply,
        **_: Any,
    ) -> dict[str, Any]:
        return dict(reply.payload)

    def _resolve_current_run_id(
        self: StudyCompanionPlugin,
        _extra_args: dict[str, Any] | None = None,
    ) -> str:
        return "vision-run"

    async def _emit_answer_evaluated_event(
        self: StudyCompanionPlugin,
        **_: Any,
    ) -> None:
        return None

    plugin._build_learning_context = MethodType(_build_learning_context, plugin)
    plugin._finalize_tutor_call = MethodType(_finalize_tutor_call, plugin)
    plugin._resolve_current_run_id = MethodType(_resolve_current_run_id, plugin)
    plugin._emit_answer_evaluated_event = MethodType(
        _emit_answer_evaluated_event, plugin
    )
    return plugin


@pytest.mark.asyncio
async def test_study_explain_text_rejects_vision_when_disabled() -> None:
    plugin = _make_plugin_for_explain(vision_enabled=False)

    result = await plugin.study_explain_text(
        text="hello",
        vision_image_base64=JPEG_IMAGE_BASE64,
    )

    assert isinstance(result, Err)
    assert "llm_vision_enabled" in str(result.error)


@pytest.mark.asyncio
async def test_study_explain_text_rejects_invalid_vision_mime() -> None:
    plugin = _make_plugin_for_explain(vision_enabled=True)

    result = await plugin.study_explain_text(
        text="hello",
        vision_image_base64="data:image/webp;base64,abc123",
    )

    assert isinstance(result, Err)
    assert "JPEG/PNG" in str(result.error)


@pytest.mark.asyncio
async def test_study_explain_text_rejects_invalid_vision_base64() -> None:
    plugin = _make_plugin_for_explain(vision_enabled=True)

    result = await plugin.study_explain_text(
        text="hello",
        vision_image_base64="!!!not-base64!!!",
    )

    assert isinstance(result, Err)
    assert "valid base64" in str(result.error)


@pytest.mark.asyncio
async def test_study_explain_text_accepts_valid_vision_image() -> None:
    plugin = _make_plugin_for_explain(vision_enabled=True)

    result = await plugin.study_explain_text(
        text="describe this",
        vision_image_base64=JPEG_IMAGE_BASE64,
    )

    assert isinstance(result, Ok)


@pytest.mark.asyncio
async def test_study_explain_text_uses_prompt_for_image_only() -> None:
    plugin = _make_plugin_for_explain(vision_enabled=True)

    result = await plugin.study_explain_text(vision_image_base64=JPEG_IMAGE_BASE64)

    assert isinstance(result, Ok)
    text, context, _mode = plugin._agent.explanations[-1]
    assert text == IMAGE_ONLY_EXPLAIN_PROMPT_ZH_CN
    assert context["source"] == "vision_image"
    assert context["source_text"] == text
    assert context["vision_image_base64"] == f"data:image/jpeg;base64,{JPEG_IMAGE_BASE64}"


@pytest.mark.asyncio
async def test_study_explain_text_prefers_pasted_image_over_stale_ocr() -> None:
    plugin = _make_plugin_for_explain(vision_enabled=True)
    plugin._state.last_ocr_text = "stale OCR text"

    result = await plugin.study_explain_text(vision_image_base64=JPEG_IMAGE_BASE64)

    assert isinstance(result, Ok)
    text, context, _mode = plugin._agent.explanations[-1]
    assert text == IMAGE_ONLY_EXPLAIN_PROMPT_ZH_CN
    assert context["source"] == "vision_image"
    assert context["source_text"] == text
    assert "stale OCR text" not in text


@pytest.mark.asyncio
async def test_study_explain_text_uses_english_prompt_for_image_only() -> None:
    plugin = _make_plugin_for_explain(vision_enabled=True, language="en")

    result = await plugin.study_explain_text(vision_image_base64=JPEG_IMAGE_BASE64)

    assert isinstance(result, Ok)
    text, context, _mode = plugin._agent.explanations[-1]
    assert text == IMAGE_ONLY_EXPLAIN_PROMPT_EN
    assert context["source"] == "vision_image"
    assert context["source_text"] == text


@pytest.mark.asyncio
async def test_study_generate_question_accepts_valid_vision_image() -> None:
    plugin = _make_plugin_for_structured_vision(vision_enabled=True)

    result = await plugin.study_generate_question(
        text="make a question from this",
        vision_image_base64=JPEG_IMAGE_BASE64,
    )

    assert isinstance(result, Ok)
    assert plugin._agent.generated_questions[-1][1][
        "vision_image_base64"
    ] == f"data:image/jpeg;base64,{JPEG_IMAGE_BASE64}"


@pytest.mark.asyncio
async def test_study_generate_question_allows_image_only() -> None:
    plugin = _make_plugin_for_structured_vision(vision_enabled=True)

    result = await plugin.study_generate_question(vision_image_base64=JPEG_IMAGE_BASE64)

    assert isinstance(result, Ok)
    assert plugin._agent.generated_questions[-1][0] == "请根据这张图片生成一道学习题。"
    assert plugin._agent.generated_questions[-1][1]["source"] == "vision_image"
    assert (
        plugin._agent.generated_questions[-1][1]["source_text"]
        == plugin._agent.generated_questions[-1][0]
    )
    assert plugin._agent.generated_questions[-1][1][
        "vision_image_base64"
    ] == f"data:image/jpeg;base64,{JPEG_IMAGE_BASE64}"


@pytest.mark.asyncio
async def test_study_generate_question_prefers_pasted_image_over_stale_ocr() -> None:
    plugin = _make_plugin_for_structured_vision(vision_enabled=True)
    plugin._state.last_ocr_text = "stale OCR text"

    result = await plugin.study_generate_question(vision_image_base64=JPEG_IMAGE_BASE64)

    assert isinstance(result, Ok)
    assert plugin._agent.generated_questions[-1][0] == "请根据这张图片生成一道学习题。"
    assert plugin._agent.generated_questions[-1][1]["source"] == "vision_image"
    assert plugin._agent.generated_questions[-1][1]["source_text"] == (
        plugin._agent.generated_questions[-1][0]
    )


@pytest.mark.asyncio
async def test_study_generate_question_uses_english_prompt_for_image_only() -> None:
    plugin = _make_plugin_for_structured_vision(vision_enabled=True, language="en")

    result = await plugin.study_generate_question(vision_image_base64=JPEG_IMAGE_BASE64)

    assert isinstance(result, Ok)
    assert (
        plugin._agent.generated_questions[-1][0]
        == "Generate a study question from the pasted image."
    )
    assert plugin._agent.generated_questions[-1][1]["source"] == "vision_image"


@pytest.mark.asyncio
async def test_study_generate_question_rejects_vision_when_disabled() -> None:
    plugin = _make_plugin_for_structured_vision(vision_enabled=False)

    result = await plugin.study_generate_question(
        text="make a question",
        vision_image_base64=JPEG_IMAGE_BASE64,
    )

    assert isinstance(result, Err)
    assert "llm_vision_enabled" in str(result.error)


@pytest.mark.asyncio
async def test_study_generate_question_rejects_invalid_vision_mime() -> None:
    plugin = _make_plugin_for_structured_vision(vision_enabled=True)

    result = await plugin.study_generate_question(
        text="make a question",
        vision_image_base64="data:image/webp;base64,abc123",
    )

    assert isinstance(result, Err)
    assert "JPEG/PNG" in str(result.error)


@pytest.mark.asyncio
async def test_study_generate_question_wraps_context_failures() -> None:
    plugin = _make_plugin_for_structured_vision(vision_enabled=True)

    async def _build_learning_context(
        self: StudyCompanionPlugin,
        operation: str,
        *,
        input_text: str = "",
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise RuntimeError(f"context failed for {operation}:{input_text}:{extra}")

    plugin._build_learning_context = MethodType(_build_learning_context, plugin)

    result = await plugin.study_generate_question(text="make a question")

    assert isinstance(result, Err)
    assert "context failed" in str(result.error)
    assert any("study_generate_question" in warning[0] for warning in plugin.logger.warnings)


@pytest.mark.asyncio
async def test_study_evaluate_answer_accepts_valid_vision_image() -> None:
    plugin = _make_plugin_for_structured_vision(vision_enabled=True)

    result = await plugin.study_evaluate_answer(
        question="What is shown?",
        answer="A diagram",
        vision_image_base64=JPEG_IMAGE_BASE64,
    )

    assert isinstance(result, Ok)
    assert plugin._agent.evaluations[-1][3][
        "vision_image_base64"
    ] == f"data:image/jpeg;base64,{JPEG_IMAGE_BASE64}"


@pytest.mark.asyncio
async def test_study_evaluate_answer_rejects_vision_when_disabled() -> None:
    plugin = _make_plugin_for_structured_vision(vision_enabled=False)

    result = await plugin.study_evaluate_answer(
        question="What is shown?",
        answer="A diagram",
        vision_image_base64=JPEG_IMAGE_BASE64,
    )

    assert isinstance(result, Err)
    assert "llm_vision_enabled" in str(result.error)


@pytest.mark.asyncio
async def test_study_evaluate_answer_preserves_missing_question_error() -> None:
    plugin = _make_plugin_for_structured_vision(vision_enabled=True)

    result = await plugin.study_evaluate_answer(
        answer="A diagram",
        vision_image_base64=JPEG_IMAGE_BASE64,
    )

    assert isinstance(result, Err)
    assert "requires a question" in str(result.error)
