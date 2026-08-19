from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest

from plugin.plugins.study_companion.study_model_gateway import (
    StudyModelError,
    StudyModelGateway,
    StudyModelResult,
    StudyModelRuntimeSnapshot,
    _runtime_transport,
)


pytestmark = pytest.mark.unit


@pytest.mark.parametrize("port", ["notaport", "70000"])
def test_runtime_transport_rejects_invalid_ports(port: str) -> None:
    assert (
        _runtime_transport(
            "qwen-plus",
            f"https://dashscope.aliyuncs.com:{port}/api/v1",
            "openai_compatible",
        )
        == "unsupported"
    )


class _Logger:
    def warning(self, *_args: object, **_kwargs: object) -> None:
        return None


def _runtime(
    *,
    group: str = "agent",
    provider_type: str = "openai_compatible",
    transport: str = "openai_compatible",
) -> StudyModelRuntimeSnapshot:
    return StudyModelRuntimeSnapshot(
        model_group=group,
        model="provider-model",
        provider_type=provider_type,
        transport=transport,
        api_key="private-key",
        base_url="https://provider.example/v1",
    )


class _QuotaManager:
    def __init__(
        self,
        *,
        allowed: bool = True,
        reserved: int = 2,
        runtime_config: dict[str, object] | None = None,
    ) -> None:
        self.allowed = allowed
        self.reserved = reserved
        self.runtime_config = runtime_config or {
            "model": "provider-model",
            "provider_type": "openai_compatible",
            "api_key": "private-key",
            "base_url": "https://provider.example/v1",
        }
        self.calls: list[tuple[str, int]] = []
        self.reserve_calls: list[tuple[str, int, int]] = []

    async def aget_model_api_config(self, _group: str) -> dict[str, object]:
        return dict(self.runtime_config)

    async def aconsume_agent_daily_quota(
        self, source: str = "", units: int = 1
    ) -> tuple[bool, dict[str, object]]:
        self.calls.append((source, units))
        return self.allowed, {}

    async def areserve_agent_daily_quota(
        self, source: str = "", units: int = 1, minimum_units: int = 1
    ) -> tuple[int, dict[str, object]]:
        self.reserve_calls.append((source, units, minimum_units))
        return self.reserved, {"remaining": 0}


def _install_manager(monkeypatch: pytest.MonkeyPatch, manager: object) -> None:
    from plugin.plugins.study_companion import study_model_gateway as module

    monkeypatch.setattr(
        module,
        "_config_manager_module",
        SimpleNamespace(get_config_manager=lambda: manager),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", ["openai_compatible", "anthropic"])
async def test_generic_provider_disables_sdk_retries_honors_budget_and_closes(
    monkeypatch: pytest.MonkeyPatch, provider_type: str
) -> None:
    from plugin.plugins.study_companion import study_model_gateway as module

    seen: dict[str, Any] = {}

    class Client:
        closed = False

        async def ainvoke(self, messages: list[dict[str, Any]]) -> object:
            seen["messages"] = messages
            return SimpleNamespace(
                content="provider-neutral answer",
                response_metadata={
                    "finish_reason": "stop",
                    "request_id": "req-generic",
                    "token_usage": {
                        "prompt_tokens": 9,
                        "completion_tokens": 3,
                    },
                },
            )

        async def aclose(self) -> None:
            self.closed = True

    client = Client()

    async def factory(**kwargs: Any) -> Client:
        seen.update(kwargs)
        return client

    manager = _QuotaManager()
    _install_manager(monkeypatch, manager)
    monkeypatch.setattr(module, "create_chat_llm_async", factory)
    gateway = StudyModelGateway(logger=_Logger())
    messages = [{"role": "user", "content": "hello"}]
    result = await gateway.call(
        messages,
        operation="concept_explain",
        deadline=time.monotonic() + 12,
        runtime=_runtime(provider_type=provider_type, transport=provider_type),
    )

    assert result.text == "provider-neutral answer"
    assert result.model_group == "agent"
    assert (result.input_tokens, result.output_tokens) == (9, 3)
    assert seen["provider_type"] == provider_type
    assert seen["max_retries"] == 0
    assert seen["max_completion_tokens"] == 3072
    assert 0 < seen["timeout"] <= 12.0 + 1e-6
    assert seen["messages"] == messages
    assert client.closed is True
    assert manager.calls == [("study_companion:concept_explain", 1)]


@pytest.mark.asyncio
async def test_dashscope_native_remains_on_native_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.study_companion import study_model_gateway as module

    async def forbidden_factory(**_kwargs: Any) -> object:
        raise AssertionError("DashScope native must not use generic factory")

    class Native:
        async def call(self, messages: list[dict[str, Any]], **kwargs: Any) -> object:
            assert messages == [{"role": "user", "content": "hello"}]
            assert kwargs["api_config"]["base_url"].endswith("/api/v1")
            return SimpleNamespace(
                text="native answer",
                model="qwen-plus",
                model_group="agent",
                request_id="native-request",
                input_tokens=2,
                output_tokens=1,
                finish_reason="stop",
                max_output_tokens=3072,
                output_limit_reached=False,
                reasoning_tokens=0,
                text_tokens=1,
                termination_unknown=False,
            )

    manager = _QuotaManager()
    _install_manager(monkeypatch, manager)
    monkeypatch.setattr(module, "create_chat_llm_async", forbidden_factory)
    gateway = StudyModelGateway(logger=_Logger())
    gateway.native_client = Native()  # type: ignore[assignment]
    runtime = StudyModelRuntimeSnapshot(
        model_group="agent",
        model="qwen-plus",
        provider_type="openai_compatible",
        transport="dashscope_native",
        api_key="private",
        base_url="https://dashscope.aliyuncs.com/api/v1",
    )

    result = await gateway.call(
        [{"role": "user", "content": "hello"}],
        operation="concept_explain",
        deadline=time.monotonic() + 10,
        runtime=runtime,
    )

    assert result.text == "native answer"
    assert manager.calls == [("study_companion:concept_explain", 1)]


@pytest.mark.asyncio
async def test_websocket_provider_is_rejected_before_quota_or_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _QuotaManager()
    _install_manager(monkeypatch, manager)
    gateway = StudyModelGateway(logger=_Logger())

    with pytest.raises(StudyModelError) as raised:
        await gateway.call(
            [{"role": "user", "content": "hello"}],
            operation="concept_explain",
            deadline=time.monotonic() + 10,
            runtime=_runtime(provider_type="websocket", transport="unsupported"),
        )

    assert raised.value.diagnostic == "unsupported_provider"
    assert manager.calls == []


@pytest.mark.asyncio
async def test_vision_request_keeps_image_and_does_not_consume_agent_quota(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.study_companion import study_model_gateway as module

    image_url = "data:image/png;base64,aGVsbG8="
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "explain"},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        }
    ]
    seen: dict[str, Any] = {}

    class Client:
        async def ainvoke(self, received: object) -> object:
            seen["messages"] = received
            return SimpleNamespace(
                content="visual answer",
                response_metadata={"finish_reason": "stop"},
            )

        async def aclose(self) -> None:
            return None

    async def factory(**_kwargs: Any) -> Client:
        return Client()

    manager = _QuotaManager()
    _install_manager(monkeypatch, manager)
    monkeypatch.setattr(module, "create_chat_llm_async", factory)
    gateway = StudyModelGateway(logger=_Logger())

    result = await gateway.call(
        messages,
        operation="concept_explain",
        deadline=time.monotonic() + 10,
        runtime=_runtime(group="vision"),
    )

    assert result.model_group == "vision"
    assert image_url in repr(seen["messages"])
    assert manager.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "group", "diagnostic"),
    [
        ("maximum context length exceeded", "agent", "context_limit_exceeded"),
        ("this model does not support image input", "vision", "vision_not_supported"),
    ],
)
async def test_provider_errors_preserve_context_and_vision_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    message: str,
    group: str,
    diagnostic: str,
) -> None:
    from plugin.plugins.study_companion import study_model_gateway as module

    class Client:
        async def ainvoke(self, _messages: object) -> object:
            raise RuntimeError(message)

        async def aclose(self) -> None:
            return None

    async def factory(**_kwargs: Any) -> Client:
        return Client()

    manager = _QuotaManager()
    _install_manager(monkeypatch, manager)
    monkeypatch.setattr(module, "create_chat_llm_async", factory)
    messages: list[dict[str, Any]] = [{"role": "user", "content": "hello"}]
    if group == "vision":
        messages[0]["content"] = [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,eA=="}}
        ]

    with pytest.raises(StudyModelError) as raised:
        await StudyModelGateway(logger=_Logger()).call(
            messages,
            operation="concept_explain",
            deadline=time.monotonic() + 10,
            runtime=_runtime(group=group),
        )
    assert raised.value.diagnostic == diagnostic


@pytest.mark.asyncio
async def test_quota_denial_prevents_actual_agent_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.study_companion import study_model_gateway as module

    calls = 0

    async def factory(**_kwargs: Any) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("quota denial must prevent network request")

    manager = _QuotaManager(allowed=False)
    _install_manager(monkeypatch, manager)
    monkeypatch.setattr(module, "create_chat_llm_async", factory)

    with pytest.raises(StudyModelError) as raised:
        await StudyModelGateway(logger=_Logger()).call(
            [{"role": "user", "content": "hello"}],
            operation="question_generate",
            deadline=time.monotonic() + 10,
            runtime=_runtime(),
        )

    assert raised.value.diagnostic == "agent_quota_exceeded"
    assert manager.calls == [("study_companion:question_generate", 1)]
    assert calls == 0


@pytest.mark.asyncio
async def test_optional_agent_reservation_preserves_last_credit_for_primary_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.study_companion import study_model_gateway as module

    class Client:
        async def ainvoke(self, _messages: object) -> object:
            return SimpleNamespace(
                content="primary answer",
                response_metadata={"finish_reason": "stop"},
            )

        async def aclose(self) -> None:
            return None

    async def factory(**_kwargs: Any) -> Client:
        return Client()

    manager = _QuotaManager(reserved=1)
    _install_manager(monkeypatch, manager)
    monkeypatch.setattr(module, "create_chat_llm_async", factory)
    gateway = StudyModelGateway(logger=_Logger())

    optional_allowed, reservation = await gateway.reserve_optional_agent_call(
        "knowledge_semantic_route"
    )
    result = await gateway.call(
        [{"role": "user", "content": "explain"}],
        operation="concept_explain",
        deadline=time.monotonic() + 10,
        runtime=_runtime(),
        quota_reservation=reservation,
    )

    assert optional_allowed is False
    assert result.text == "primary answer"
    assert manager.reserve_calls == [
        ("study_companion:knowledge_semantic_route", 2, 1)
    ]
    assert manager.calls == []


@pytest.mark.asyncio
async def test_optional_agent_reservation_validates_runtime_before_charging_quota(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _QuotaManager(
        runtime_config={
            "model": "provider-model",
            "provider_type": "openai_compatible",
            "api_key": "",
            "base_url": "https://provider.example/v1",
        }
    )
    _install_manager(monkeypatch, manager)

    with pytest.raises(StudyModelError) as raised:
        await StudyModelGateway(logger=_Logger()).reserve_optional_agent_call(
            "knowledge_semantic_route"
        )

    assert raised.value.diagnostic == "authentication_failed"
    assert manager.reserve_calls == []


def test_runtime_description_and_repr_never_expose_secret_or_endpoint() -> None:
    runtime = _runtime()
    description = runtime.safe_description()

    assert description["group"] == "agent"
    assert description["model"] == "provider-model"
    assert description["credential_configured"] is True
    assert "api_key" not in description
    assert "base_url" not in description
    assert "private-key" not in repr(runtime)
    assert "provider.example" not in repr(runtime)


@pytest.mark.asyncio
async def test_long_document_job_resolves_and_binds_one_agent_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    from contextlib import contextmanager

    from plugin.plugins.study_companion import StudyCompanionPlugin
    from plugin.plugins.study_companion.document_analysis_jobs import (
        DocumentAnalysisJobManager,
    )
    from plugin.plugins.study_companion.models import TutorReply
    from plugin.sdk.plugin import Ok

    snapshots = [_runtime(), _runtime(provider_type="anthropic", transport="anthropic")]
    resolve_calls: list[str] = []
    bound: list[StudyModelRuntimeSnapshot] = []

    class Agent:
        async def resolve_model_runtime(self, group: str) -> StudyModelRuntimeSnapshot:
            resolve_calls.append(group)
            return snapshots[len(resolve_calls) - 1]

        @contextmanager
        def bind_model_runtime(self, runtime: StudyModelRuntimeSnapshot):
            bound.append(runtime)
            yield runtime

        async def document_analyze(self, document: object) -> TutorReply:
            assert bound == [snapshots[0]]
            return TutorReply(
                operation="document_analyze",
                input_text=getattr(document, "descriptor"),
                reply="safe analysis",
            )

    class Owner:
        _agent = Agent()

        def __init__(self) -> None:
            self._document_jobs = DocumentAnalysisJobManager()

        _document_job_manager = StudyCompanionPlugin._document_job_manager

        async def _finalize_tutor_call(
            self, operation: str, reply: TutorReply, **kwargs: Any
        ) -> dict[str, Any]:
            return {
                "operation": operation,
                "reply": reply.reply,
                "summary": reply.reply,
                "document": kwargs["public_payload"]["document"],
                "degraded": False,
                "diagnostic": "",
            }

    owner = Owner()
    result = await StudyCompanionPlugin.study_start_document_analysis(
        owner,
        document_name="notes.txt",
        document_type="text/plain",
        document_text="safe source",
        locale="en",
    )
    assert isinstance(result, Ok)
    for _ in range(30):
        status = await owner._document_jobs.status(result.value["job_id"])
        if status["status"] != "running":
            break
        await asyncio.sleep(0)

    assert status["status"] == "completed"
    assert resolve_calls == ["agent"]
    assert bound == [snapshots[0]]
    await owner._document_jobs.shutdown()
