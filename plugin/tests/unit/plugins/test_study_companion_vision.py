from __future__ import annotations

import asyncio
import base64
import json
import threading
from types import MethodType, SimpleNamespace
from typing import Any

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
    assert "Solution Process" in IMAGE_ONLY_EXPLAIN_PROMPT_EN
    assert "Final Answer" in IMAGE_ONLY_EXPLAIN_PROMPT_EN
    assert "brief analysis" in IMAGE_ONLY_EXPLAIN_PROMPT_EN
    assert "Problem Analysis" in IMAGE_ONLY_EXPLAIN_PROMPT_EN
    assert "Transfer Practice" in IMAGE_ONLY_EXPLAIN_PROMPT_EN
    assert "do not assume it is single-select" in IMAGE_ONLY_EXPLAIN_PROMPT_EN
    assert "all correct option letters" in IMAGE_ONLY_EXPLAIN_PROMPT_EN
    assert "题目解析" in IMAGE_ONLY_EXPLAIN_PROMPT_ZH_CN
    assert "解题过程" in IMAGE_ONLY_EXPLAIN_PROMPT_ZH_CN
    assert "答案" in IMAGE_ONLY_EXPLAIN_PROMPT_ZH_CN
    assert "举一反三" in IMAGE_ONLY_EXPLAIN_PROMPT_ZH_CN
    assert "解析" in IMAGE_ONLY_EXPLAIN_PROMPT_ZH_CN
    assert "不要默认是单选题" in IMAGE_ONLY_EXPLAIN_PROMPT_ZH_CN
    assert "输出全部正确选项" in IMAGE_ONLY_EXPLAIN_PROMPT_ZH_CN


@pytest.mark.parametrize(
    "model, expected",
    [
        ("gpt-4o", True),
        ("claude-4-sonnet", True),
        ("gemini-2.5-pro", True),
        ("glm-4.6v", True),
        ("glm-5v-turbo", True),
        ("glm-4-plus", False),
        ("gpt-4", False),
        ("", False),
    ],
)
def test_model_supports_vision(model: str, expected: bool) -> None:
    assert TutorLLMAgent._model_supports_vision(model) is expected


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


def test_strip_image_content_removes_image_blocks() -> None:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "one"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
                {"type": "text", "text": "two"},
            ],
        },
        {"role": "assistant", "content": "unchanged"},
    ]

    result = TutorLLMAgent._strip_image_content(messages)

    assert result == [
        {"role": "user", "content": "one\ntwo"},
        {"role": "assistant", "content": "unchanged"},
    ]


@pytest.mark.asyncio
async def test_concept_explain_attaches_vision_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="en"))
    seen: list[dict[str, Any]] = []

    async def _fake_call_model(messages: list[dict[str, Any]]):
        seen.extend(messages)
        return "vision reply"

    monkeypatch.setattr(agent, "_call_model", _fake_call_model)

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

    async def _fake_call_model(_messages: list[dict[str, Any]]):
        return "题目解析\n分析题意。\n\n解题过程\n列式计算。\n\n答案\nA"

    monkeypatch.setattr(agent, "_call_model", _fake_call_model)

    reply = await agent.concept_explain(
        IMAGE_ONLY_EXPLAIN_PROMPT_ZH_CN,
        context={"vision_image_base64": JPEG_IMAGE_BASE64},
    )

    expected_tail = "举一反三\n" + ZH_TRANSFER_EXPECTED_TEXT
    assert reply.reply.rstrip().endswith(expected_tail)
    assert reply.reply.count("举一反三") == 1


@pytest.mark.asyncio
async def test_concept_explain_appends_transfer_to_numbered_zh_solution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="zh-CN"))

    async def _fake_call_model(_messages: list[dict[str, Any]]):
        return "1. 分析条件。\n\n2. 计算总和。\n\n答案\nA"

    monkeypatch.setattr(agent, "_call_model", _fake_call_model)

    reply = await agent.concept_explain(
        IMAGE_ONLY_EXPLAIN_PROMPT_ZH_CN,
        context={"vision_image_base64": JPEG_IMAGE_BASE64},
    )

    assert reply.reply.rstrip().endswith("举一反三\n" + ZH_TRANSFER_EXPECTED_TEXT)


@pytest.mark.asyncio
async def test_concept_explain_appends_transfer_when_reply_is_zh_but_locale_is_en(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="en"))

    async def _fake_call_model(_messages: list[dict[str, Any]]):
        return "4. 计算期望并验证\n\n对分数进行约分。\n\n答案\nA"

    monkeypatch.setattr(agent, "_call_model", _fake_call_model)

    reply = await agent.concept_explain(
        IMAGE_ONLY_EXPLAIN_PROMPT_ZH_CN,
        context={"vision_image_base64": JPEG_IMAGE_BASE64},
    )

    assert reply.reply.rstrip().endswith("举一反三\n" + ZH_TRANSFER_EXPECTED_TEXT)


@pytest.mark.asyncio
async def test_concept_explain_appends_transfer_to_option_verification_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="zh-CN"))

    async def _fake_call_model(_messages: list[dict[str, Any]]):
        return (
            "验证 C：若 AB ⊥ CD，则 CD ⊥ 平面 ABD。\n\n"
            "结论：C 错误。\n\n"
            "验证 D：若 AB ⊥ 平面 ACD，则 AC ⊥ AD。\n\n"
            "结论：D 错误。\n\n"
            "结论：B 正确。"
        )

    monkeypatch.setattr(agent, "_call_model", _fake_call_model)

    reply = await agent.concept_explain(
        IMAGE_ONLY_EXPLAIN_PROMPT_ZH_CN,
        context={"vision_image_base64": JPEG_IMAGE_BASE64},
    )

    assert reply.reply.rstrip().endswith("举一反三\n" + ZH_TRANSFER_EXPECTED_TEXT)


@pytest.mark.asyncio
async def test_concept_explain_vision_failure_uses_image_specific_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = TutorLLMAgent(logger=_Logger(), config=StudyConfig(language="zh-CN"))

    async def _broken_call_model(_messages: list[dict[str, Any]]):
        raise RuntimeError("llm unavailable")

    monkeypatch.setattr(agent, "_call_model", _broken_call_model)

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
        messages: list[dict[str, Any]], *, operation: str = "question_generate"
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
async def test_call_model_strips_vision_for_text_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from utils import config_manager, llm_client, token_tracker

    seen: list[dict[str, Any]] = []
    config_groups: list[str] = []
    call_types: list[str] = []
    logger = _Logger()

    class _ConfigManager:
        def get_model_api_config(self, group: str) -> dict[str, str]:
            config_groups.append(group)
            if group == "vision":
                return {
                    "base_url": "",
                    "model": "",
                    "api_key": "",
                }
            return {
                "base_url": "https://llm.example.test/v1",
                "model": "gpt-4",
                "api_key": "key",
            }

    class _FakeLLM:
        async def ainvoke(self, messages: list[dict[str, Any]]):
            seen.extend(messages)
            return SimpleNamespace(content="reply")

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _ConfigManager())
    monkeypatch.setattr(llm_client, "create_chat_llm", lambda **_kwargs: _FakeLLM())
    monkeypatch.setattr(token_tracker, "set_call_type", call_types.append)
    agent = TutorLLMAgent(logger=logger, config=StudyConfig(language="en"))

    result = await agent._call_model(
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

    assert result == "reply"
    assert config_groups == ["vision", "agent"]
    assert call_types == ["agent"]
    assert seen == [{"role": "user", "content": "one"}]
    assert logger.warnings
    assert "vision stripped" in str(logger.warnings[0][0][0])


@pytest.mark.asyncio
async def test_call_model_uses_vision_config_for_image_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from utils import config_manager, llm_client, token_tracker

    seen_messages: list[dict[str, Any]] = []
    seen_client_kwargs: list[dict[str, Any]] = []
    config_groups: list[str] = []
    call_types: list[str] = []

    class _ConfigManager:
        def get_model_api_config(self, group: str) -> dict[str, str]:
            config_groups.append(group)
            if group == "vision":
                return {
                    "base_url": "https://vision.example.test/v1",
                    "model": "step-1o-turbo-vision",
                    "api_key": "vision-key",
                }
            return {
                "base_url": "https://agent.example.test/v1",
                "model": "step-3",
                "api_key": "agent-key",
            }

    class _FakeLLM:
        async def ainvoke(self, messages: list[dict[str, Any]]):
            seen_messages.extend(messages)
            return SimpleNamespace(content="vision reply")

    def _create_chat_llm(**kwargs: Any) -> _FakeLLM:
        seen_client_kwargs.append(kwargs)
        return _FakeLLM()

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _ConfigManager())
    monkeypatch.setattr(llm_client, "create_chat_llm", _create_chat_llm)
    monkeypatch.setattr(token_tracker, "set_call_type", call_types.append)
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
        ]
    )

    assert result == "vision reply"
    assert config_groups == ["vision"]
    assert call_types == ["vision"]
    assert seen_client_kwargs[0]["base_url"] == "https://vision.example.test/v1"
    assert seen_client_kwargs[0]["model"] == "step-1o-turbo-vision"
    assert seen_client_kwargs[0]["api_key"] == "vision-key"
    content = seen_messages[0]["content"]
    assert isinstance(content, list)
    assert content[1]["type"] == "image_url"


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
